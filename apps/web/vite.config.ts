import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const rootEnvDir = fileURLToPath(new URL('../../', import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, rootEnvDir, 'VITE_');
  const localEnv = loadEnv(mode, fileURLToPath(new URL('.', import.meta.url)), 'VITE_');
  return {
    plugins: [react()],
    envDir: fileURLToPath(new URL('.', import.meta.url)),
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: localEnv.VITE_BFF_TARGET || env.VITE_BFF_TARGET || 'http://127.0.0.1:3000',
          changeOrigin: false,
        },
      },
    },
  };
});
