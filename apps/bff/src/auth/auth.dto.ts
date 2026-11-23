import { IsIn } from 'class-validator';

export class DemoLoginDto {
  @IsIn(['employee_a', 'employee_b', 'finance'])
  account_key!: 'employee_a' | 'employee_b' | 'finance';
}
