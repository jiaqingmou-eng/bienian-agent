import {
  IsHash,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Max,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import { Transform, Type } from 'class-transformer';

export class AgentRunListQueryDto {
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100)
  limit = 20;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(0)
  offset = 0;
}

export class StartAgentRunDto {
  @IsUUID()
  client_request_id!: string;

  @Transform(({ value }: { value: unknown }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MinLength(1)
  @MaxLength(2000)
  message!: string;
}

export class AgentMessageDto {
  @IsUUID()
  message_id!: string;

  @IsInt()
  @Min(0)
  expected_version!: number;

  @Transform(({ value }: { value: unknown }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MinLength(1)
  @MaxLength(2000)
  message!: string;
}

export class AgentVersionDto {
  @IsInt()
  @Min(0)
  expected_version!: number;
}

export class AgentConfirmDto extends AgentVersionDto {
  @IsUUID()
  confirmation_id!: string;

  @IsHash('sha256')
  content_hash!: string;
}
