import '@ant-design/v5-patch-for-react-19';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { App } from './app/App';
import { AuthProvider } from './app/auth';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: {
      colorPrimary: '#28634f', colorInfo: '#28634f', borderRadius: 10,
      fontFamily: 'Inter, "Microsoft YaHei", "PingFang SC", sans-serif',
    } }}>
      <BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
