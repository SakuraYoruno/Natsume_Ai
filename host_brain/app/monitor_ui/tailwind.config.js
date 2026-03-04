/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Dify 设计 token
        primary: {
          DEFAULT: '#2970FF',
          50:  '#EEF4FF',
          100: '#D9E8FF',
          200: '#B2D0FF',
          300: '#84B0FF',
          400: '#5690FF',
          500: '#2970FF',
          600: '#1D5CE6',
          700: '#1448C2',
          800: '#0E369E',
          900: '#092680',
        },
        // 状态色
        success: '#12B76A',
        warning: '#F79009',
        danger:  '#F04438',
        // 中性色（Dify 风格浅灰）
        gray: {
          25:  '#FCFCFD',
          50:  '#F9FAFB',
          100: '#F2F4F7',
          200: '#EAECF0',
          300: '#D0D5DD',
          400: '#98A2B3',
          500: '#667085',
          600: '#475467',
          700: '#344054',
          800: '#1D2939',
          900: '#101828',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'Roboto',
          '"Helvetica Neue"',
          'Arial',
          '"Noto Sans SC"',
          'sans-serif',
        ],
        mono: [
          '"JetBrains Mono"',
          '"Fira Code"',
          'Consolas',
          'monospace',
        ],
      },
      boxShadow: {
        card: '0 1px 3px 0 rgba(16,24,40,0.1), 0 1px 2px -1px rgba(16,24,40,0.1)',
        'card-hover': '0 4px 8px -2px rgba(16,24,40,0.1), 0 2px 4px -2px rgba(16,24,40,0.06)',
        dropdown: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)',
      },
      borderRadius: {
        DEFAULT: '8px',
        sm: '6px',
        lg: '12px',
        xl: '16px',
      },
    },
  },
  plugins: [],
}
