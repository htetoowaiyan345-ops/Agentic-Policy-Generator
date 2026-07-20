import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1'
  },
  // CKEditor 5 ships ESM and is pre-bundled by Vite out of the box.
  // No manual `optimizeDeps.include` entries are required.
  optimizeDeps: {
    include: ['ckeditor5']
  }
});