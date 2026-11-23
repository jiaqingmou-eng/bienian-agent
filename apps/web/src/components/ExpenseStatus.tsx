import { Tag } from 'antd';
import type { ExpenseStatus } from '../api/types';

const statuses = {
  draft: { label: '草稿', color: 'default' },
  pending: { label: '待审批', color: 'gold' },
  approved: { label: '已批准', color: 'green' },
  rejected: { label: '已驳回', color: 'red' },
};

export function ExpenseStatusTag({ status }: { status: ExpenseStatus }) {
  return <Tag color={statuses[status].color}>{statuses[status].label}</Tag>;
}
