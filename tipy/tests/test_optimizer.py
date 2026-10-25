import unittest

from tipy.optimizer import optimize


class OptimizerTests(unittest.TestCase):
    def test_redundant_stack_pairs_are_removed_until_stable(self):
        self.assertEqual(optimize("push hl\npush de\npop de\npop hl\nret\n"), "ret\n")
        source = "push hl\n__L1:\npop hl\n"
        self.assertEqual(optimize(source), source)

    def test_simple_rhs_uses_de_without_stack_or_exchange(self):
        for value in ["7", "65535", "__heap_end", "(__var_0)"]:
            source = f"push hl\nld hl,{value}\nex de,hl\npop hl\nadd hl,de\n"
            self.assertEqual(optimize(source), f"        ld de,{value}\nadd hl,de\n")

    def test_calls_and_nested_expressions_keep_their_stack(self):
        for rhs in ["call __fn_0", "ld hl,0\nadd hl,sp"]:
            source = f"push hl\n{rhs}\nex de,hl\npop hl\n"
            self.assertEqual(optimize(source), source)

    def test_conditional_branches_and_instruction_boundaries_are_preserved(self):
        for source in ["jp z,__L1\n__L1:\n", "jp __L1\ninc hl\n__L1:\n"]:
            self.assertEqual(optimize(source), source)
        self.assertEqual(optimize("jp __L1\n; source line\n__L0:\n__L1:\nret\n"),
                         "; source line\n__L0:\n__L1:\nret\n")
