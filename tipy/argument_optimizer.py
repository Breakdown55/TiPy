"""Keep literal scalar arguments out of evaluation snapshots and temporary RAM."""

from copy import deepcopy


def simplify(tree):
    tree = deepcopy(tree)

    def visit(node):
        if isinstance(node, list):
            return [visit(value) for value in node]
        if not isinstance(node, dict):
            return node
        node = {key: visit(value) for key, value in node.items()}
        if (
            node.get("kind") == "negate"
            and node.get("value", {}).get("kind") == "number"
            and node.get("type") == "i16"
        ):
            value = (-node["value"]["value"]) & 65535
            return {
                **node,
                "kind": "number",
                "value": value - 65536 if value >= 32768 else value,
            }
        constants = {}
        evaluations = []
        scalar_types = {"i16", "u8", "bool"}
        snapshots = node.get("evaluations", [])
        only_scalar_loads = all(
            evaluation["value"].get("kind") in {"number", "boolean", "name"}
            and evaluation["value"].get("type") in scalar_types
            and not evaluation["value"].get("cell")
            for evaluation in snapshots
        )
        for evaluation in snapshots:
            value = evaluation["value"]
            if (
                only_scalar_loads
                or value.get("kind") in {"number", "boolean"}
                and value.get("type") in scalar_types
            ):
                constants[evaluation["slot"]["label"]] = value
            else:
                evaluations.append(evaluation)
        if not constants:
            return node

        def substitute(value):
            if isinstance(value, list):
                return [substitute(item) for item in value]
            if not isinstance(value, dict):
                return value
            if value.get("kind") == "name" and value.get("label") in constants:
                return deepcopy(constants[value["label"]])
            return {key: substitute(item) for key, item in value.items()}

        for field in (
            "base",
            "arguments",
            "left",
            "right",
            "value",
            "sources",
            "values",
            "bundle",
        ):
            if field in node:
                node[field] = substitute(node[field])
        # Starred-call binders read snapshots from a later evaluation rather
        # than directly from arguments. Replace those reads before removing
        # the constant slot as well.
        node["evaluations"] = [
            {**evaluation, "value": substitute(evaluation["value"])}
            for evaluation in evaluations
        ]
        return node

    tree["body"] = visit(tree["body"])

    def labels(node):
        if isinstance(node, list):
            return set().union(*(labels(value) for value in node))
        if isinstance(node, dict):
            found = {
                node[key]
                for key in ("label", "symbol")
                if key in node and isinstance(node[key], str)
            }
            return found | set().union(*(labels(value) for value in node.values()))
        return set()

    used = labels(tree["body"])
    tree["temporaries"] = {
        name: value
        for name, value in tree.get("temporaries", {}).items()
        if name in used
    }
    return tree
