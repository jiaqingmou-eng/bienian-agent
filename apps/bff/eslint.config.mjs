import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/**', 'src/backend/backend.schema.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['test/**/*.cjs'],
    languageOptions: { globals: { process: 'readonly' } },
  },
  { rules: { '@typescript-eslint/no-explicit-any': 'error' } },
);
