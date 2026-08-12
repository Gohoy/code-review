import React from "react";
import { createRoot } from "react-dom/client";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { ReviewApp } from "./App.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#2467d8", borderRadius: 6 } }}>
      <App><ReviewApp /></App>
    </ConfigProvider>
  </React.StrictMode>,
);
