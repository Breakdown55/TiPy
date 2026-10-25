// Only use syntax implemented by the Python compiler connected to Compile.
export const DEFAULT_FILES = {
    'main.tip': `int B = 3
int C = 4
int A = B + C

A = A + (B - C)
A += 1
`,
    'utils.tip': '# Shared declarations. Use import utils in your entry file.\n'
}
