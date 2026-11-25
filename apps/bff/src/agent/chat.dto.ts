import { Transform } from 'class-transformer';
import { IsInt, IsString, IsUUID, MaxLength, Min, MinLength, ValidateIf } from 'class-validator';

/** 对话参数白名单：不接受租户、用户、目标能力或模型提示词，任务和版本必须成对出现。 */
export class ChatDto {
  @IsUUID()
  message_id!: string;

  @Transform(({ value }: { value: unknown }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MinLength(1)
  @MaxLength(2000)
  message!: string;

  @ValidateIf(
    (value: ChatDto) => value.run_id !== undefined || value.expected_version !== undefined,
  )
  @IsUUID()
  run_id?: string;

  @ValidateIf(
    (value: ChatDto) => value.run_id !== undefined || value.expected_version !== undefined,
  )
  @IsInt()
  @Min(0)
  expected_version?: number;
}
