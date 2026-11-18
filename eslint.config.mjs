// Root ESLint flat config shared by all JS/TS packages.
import js from '@eslint/js'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['**/dist', '**/node_modules', '**/coverage', '.venv', 'storage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
)
