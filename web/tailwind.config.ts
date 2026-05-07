import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Hacker News 风
        bg: "#f6f6ef",
        card: "#ffffff",
        accent: "#ff6600",
        link: "#000080",
        muted: "#828282",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Verdana",
          "system-ui",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
