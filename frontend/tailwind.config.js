/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: { 
    extend: {
      keyframes: {
        slideDownFade: {
          '0%': { transform: 'translateY(-8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        slideUpFade: {
          '0%': { transform: 'translateY(8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
      animation: {
        'slide-down-fade': 'slideDownFade 500ms ease-in-out',
        'slide-up-fade': 'slideUpFade 500ms ease-in-out',
        'spin-slow': 'spin 2s linear infinite',
      },
    }
  },
  plugins: [require('daisyui')],
  daisyui: {
    themes: [
      {
        redeemerDark: {
          primary: '#7dd3fc',           // sky-300 (actions)
          'primary-content': '#001018',
          secondary: '#a78bfa',         // violet-400
          accent: '#22c55e',            // emerald-500 (success accents)
          neutral: '#1f2937',           // slate-800
          'base-100': '#0b1220',        // page bg
          'base-200': '#0e1726',        // surfaces
          'base-300': '#131c2b',        // raised surfaces
          info: '#38bdf8',
          success: '#16a34a',
          warning: '#f59e0b',
          error: '#ef4444',
        },
      },
      'dark',
    ],
  },
}
