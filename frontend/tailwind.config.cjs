/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: 'rgb(var(--color-primary))',
        'primary-hover': 'rgb(var(--color-primary-hover))',
        accent: 'rgb(var(--color-accent))',
        'accent-hover': 'rgb(var(--color-accent-hover))',
      },
    },
  },
  plugins: [],
}
