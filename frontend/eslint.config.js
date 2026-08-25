// M0 #2 ESLint flat config（ESLint 10）
// - @eslint/js recommended + typescript-eslint recommended
// - eslint-plugin-react-hooks（recommended-latest）+ react-refresh（vite）
// - eslint-config-prettier 关闭与 Prettier 冲突的规则
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import prettier from "eslint-config-prettier";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat["recommended-latest"],
      reactRefresh.configs.vite,
      prettier,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
  },
  // shadcn/ui 生成组件会同时导出组件与 variants 常量，关闭 react-refresh 组件导出限制
  {
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
);
