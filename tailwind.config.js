/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/janus/templates/**/*.html"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'primary': '#3498db',
        'secondary': '#2c3e50',
        'accent': '#e74c3c',
        'light-bg': '#f4f7f9',
        'light-card': '#ffffff',
        'light-text': '#2c3e50',
        'dark-bg': '#1a1a1a',
        'dark-card': '#2b2b2b',
        'dark-text': '#ecf0f1',
      }
    }
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
