import js from '@eslint/js'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

const taskRuntimeFiles = [
  'src/features/execution/taskRuntimeModel.js',
  'src/features/execution/taskRuntimeApi.js',
  'src/features/execution/taskRuntimeQueries.js',
  'src/features/execution/components/TaskRuntimeConsole.jsx',
  'src/pages/TaskRuntime.jsx',
]

export default [
  {
    ignores: ['../static/monitor/**', 'test-results/**'],
  },
  {
    files: taskRuntimeFiles,
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2022 },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react/jsx-uses-vars': 'error',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['src/features/execution/taskRuntimeModel.test.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.node,
    },
    rules: js.configs.recommended.rules,
  },
]
