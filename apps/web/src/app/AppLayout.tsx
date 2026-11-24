import {
  FileTextOutlined,
  LogoutOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Layout, Menu, Space, Tag, Typography, message } from 'antd';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { describeError } from '../api/client';
import { useAuth } from './auth';

export function AppLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  if (!user) return null;
  const isFinance = user.role === 'finance';
  return (
    <Layout className="app-layout">
      <Layout.Sider width={236} breakpoint="lg" collapsedWidth={72} className="sidebar">
        <Link to="/expenses" className="brand">
          <span className="brand-mark">账</span>
          <span className="brand-name">
            知账<span>FINANCE WORKSPACE</span>
          </span>
        </Link>
        <div className="sidebar-section">工作空间</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[
            location.pathname === '/assistant'
              ? 'assistant'
              : location.pathname === '/expenses/new'
                ? 'new'
                : 'expenses',
          ]}
          onClick={({ key }) =>
            navigate(
              key === 'assistant' ? '/assistant' : key === 'new' ? '/expenses/new' : '/expenses',
            )
          }
          items={[
            { key: 'assistant', icon: <RobotOutlined />, label: '报销助手' },
            {
              key: 'expenses',
              icon: <FileTextOutlined />,
              label: isFinance ? '财务工作台' : '我的报销',
            },
            ...(!isFinance ? [{ key: 'new', icon: <PlusOutlined />, label: '新建报销' }] : []),
          ]}
        />
        <div className="sidebar-footer">
          <SafetyCertificateOutlined /> 企业财务服务
        </div>
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Typography.Text type="secondary">示例科技 · 报销管理</Typography.Text>
          <Space size="middle">
            <Tag color={isFinance ? 'purple' : 'green'}>{isFinance ? '财务' : '员工'}</Tag>
            <Avatar style={{ backgroundColor: '#dcebe4', color: '#234c3e' }}>
              {user.name.slice(-1)}
            </Avatar>
            <span>{user.name}</span>
            <Button
              type="text"
              aria-label="退出登录"
              icon={<LogoutOutlined />}
              onClick={() => {
                void logout().catch((err: unknown) => {
                  void message.error(describeError(err));
                });
              }}
            />
          </Space>
        </Layout.Header>
        <Layout.Content className="main-content">
          <Outlet key={user.id} />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
