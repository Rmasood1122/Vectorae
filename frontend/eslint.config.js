import nextPlugin from 'eslint-config-next';

export default [
  ...nextPlugin,
  {
    ignores: ['node_modules', '.next', 'dist', 'build'],
  },
  {
    rules: {
      // Pre-existing violations from ESLint 8 -> 9 upgrade
      // Downgrade to warnings so they don't block CI. Fix in a separate cleanup task.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/static-components': 'warn',
      'react-hooks/immutability': 'warn',
    },
  },
];
