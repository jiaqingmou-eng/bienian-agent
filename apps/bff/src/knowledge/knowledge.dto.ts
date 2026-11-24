import { Type } from 'class-transformer';
import {
  IsInt,
  IsOptional,
  IsString,
  Matches,
  Max,
  MaxLength,
  Min,
  MinLength,
  IsDateString,
} from 'class-validator';

/** 只接受分页参数；全局 ValidationPipe 会拒绝伪造的租户、责任人或状态字段。 */
export class DocumentListQueryDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(50)
  limit = 20;

  @Type(() => Number)
  @IsInt()
  @Min(0)
  @Max(10000)
  offset = 0;
}

/** 查询只提供问题及适用日期；不接受任意租户、状态、来源路径或排序 SQL。 */
export class SearchDocumentsDto {
  @IsString()
  @MinLength(1)
  @MaxLength(300)
  query!: string;

  @IsOptional()
  @IsString()
  @Matches(/^\d{4}-\d{2}-\d{2}$/)
  @IsDateString({ strict: true })
  as_of?: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(10)
  limit = 5;
}
