module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  testMatch: ['<rootDir>/test/**/*.spec.ts'],
  clearMocks: true,
  setupFiles: ['<rootDir>/test/setup-env.cjs'],
};
