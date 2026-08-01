import { Route, Routes } from "react-router-dom";
import WorkspacePage from "./pages/WorkspacePage";
import StaticPage from "./pages/StaticPage";
import NotFoundPage from "./pages/NotFoundPage";

export default function App() {
  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <Routes>
        <Route path="/" element={<WorkspacePage />} />
        <Route
          path="/privacy"
          element={
            <StaticPage title="隐私说明">
              <p>最近研究与匿名用户标识保存在当前浏览器。查询与上传内容会发送到当前连接的购物研究服务。</p>
              <p>清除浏览器站点数据会移除本地研究记录。你也可以在研究过程面板中清除服务保存的偏好。</p>
            </StaticPage>
          }
        />
        <Route
          path="/terms"
          element={
            <StaticPage title="使用条款">
              <p>商品价格、库存、运费与税费会随平台变化。购买前请以目标平台结算页为准。</p>
              <p>结果页会标明 Live Result、Sandbox Result 或 Partial Result。混合来源仅限开发诊断模式，沙盒样本不代表平台实时商品。</p>
            </StaticPage>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  );
}
