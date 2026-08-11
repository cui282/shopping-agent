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
              <p>最近研究与匿名购物标识保存在当前浏览器；该标识仅用于关联研究与客户主动保存的偏好，不是登录账号、认证身份或数据所有权证明。</p>
              <p>清除浏览器站点数据会移除本地研究记录。你也可以在研究过程面板中清除服务保存的偏好。</p>
            </StaticPage>
          }
        />
        <Route
          path="/terms"
          element={
            <StaticPage title="使用条款">
              <p>商品价格、库存、运费与税费会随平台变化。购买前请以目标平台结算页为准。</p>
              <p>结果页会标明实时结果、演示结果或部分平台结果。混合来源仅限开发诊断模式，演示样本不代表数据提供商通道的实时商品。</p>
            </StaticPage>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  );
}
