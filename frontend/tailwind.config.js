/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9', 100: '#eceef2', 200: '#d5d9e2', 300: '#b0b8c9',
          400: '#8591ab', 500: '#657391', 600: '#505c78', 700: '#424b61',
          800: '#394052', 900: '#181c26', 950: '#0e1118',
        },
        brand: {
          50: '#fff5ed', 100: '#ffe8d4', 200: '#ffcda8', 300: '#ffa970',
          400: '#ff7a36', 500: '#ff5a0f', 600: '#f03e05', 700: '#c72c07',
          800: '#9e250e', 900: '#7f220f', 950: '#450e04',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgba(255,90,15,0.5)' },
          '70%': { boxShadow: '0 0 0 12px rgba(255,90,15,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(255,90,15,0)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'flash': {
          '0%': { backgroundColor: 'rgba(255,90,15,0.18)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'pulse-ring': 'pulse-ring 1.6s infinite',
        'slide-up': 'slide-up 220ms ease-out',
        'flash': 'flash 900ms ease-out',
      },
    },
  },
  plugins: [],
}
