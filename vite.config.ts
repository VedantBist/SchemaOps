import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';
import { LOG_PRODUCT_NAME, ULPF_DEMO_MODE } from './src/config/uiMode';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss(), {
      name: 'demo-branding',
      transformIndexHtml(html) {
        if (!ULPF_DEMO_MODE) return html;
        return html
          .replaceAll('CausalOps - AI Root Cause Analysis &amp; Failure Prediction', `${LOG_PRODUCT_NAME} — Universal Log Pre-processing Framework`)
          .replaceAll('AI-based root cause analysis, 3D causal topology, and failure prediction operations console for cloud microservices.',
            'Collect, normalize and verify logs with traceable, lossless processing.');
      },
    }],
    resolve: {
      alias: {
        '@': path.resolve(import.meta.dirname, '.'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 3000,
      strictPort: true,
      allowedHosts: ['localhost'],
      // The UI talks only to the CausalOps API (one origin); nginx does the same in production.
      proxy: {
        '/api': { target: process.env.CAUSALOPS_API_URL ?? 'http://localhost:8080', changeOrigin: true },
      },
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
