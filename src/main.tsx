// Fonts are bundled (no runtime download): the console works in air-gapped networks.
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-sans/700.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource/jetbrains-mono/600.css';
import '@fontsource/jetbrains-mono/700.css';
import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';
import { LOG_PRODUCT_NAME, ULPF_DEMO_MODE } from './config/uiMode';

if (ULPF_DEMO_MODE) {
  document.title = `${LOG_PRODUCT_NAME} — Universal Log Pre-processing Framework`;
  document.querySelector('meta[property="og:title"]')?.setAttribute('content', document.title);
  for (const selector of ['meta[name="description"]', 'meta[property="og:description"]']) {
    document.querySelector(selector)?.setAttribute('content', 'Collect, normalize and verify logs with traceable, lossless processing.');
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
