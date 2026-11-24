// Stories are only useful for design review when they render with the real
// Tailwind build and the shell design tokens, so the app stylesheet is loaded
// here rather than in the shared preview object (which jsdom tests also import).
import '../src/index.css';

// Every app shell is dark: UIVersionProvider always puts `dark` on <html>, and
// index.css gives <body> the dark background. Stories render outside that
// provider, so without the class they got the dark page with the light-mode
// text colors of Tailwind's `dark:` variants (JAN-237).
document.documentElement.classList.add('dark');

export { default } from '../src/test/storybookPreview';
