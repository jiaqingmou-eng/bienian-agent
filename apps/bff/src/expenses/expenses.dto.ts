import { Transform, Type } from 'class-transformer';
import {
  ArrayMaxSize, ArrayMinSize, IsArray, IsDateString, IsIn, IsInt, IsNotEmpty,
  IsOptional, IsString, Matches, Max, MaxLength, Min, ValidateNested,
} from 'class-validator';

const trim = ({ value }: { value: unknown }) => typeof value === 'string' ? value.trim() : value;

export class ExpenseItemDto {
  @IsIn(['transport', 'lodging', 'meal', 'other'])
  category!: 'transport' | 'lodging' | 'meal' | 'other';

  @Matches(/^\d{4}-\d{2}-\d{2}$/)
  @IsDateString({ strict: true })
  incurred_on!: string;

  @Transform(trim)
  @IsString()
  @IsNotEmpty()
  @MaxLength(200)
  description!: string;

  // No implicit string-to-number conversion: the API requires integer fen.
  @IsInt()
  @Min(1)
  @Max(100_000_000)
  amount_fen!: number;
}

export class CreateExpenseDto {
  @Transform(trim)
  @IsString()
  @IsNotEmpty()
  @MaxLength(100)
  title!: string;

  @Transform(trim)
  @IsString()
  @IsNotEmpty()
  @MaxLength(1000)
  purpose!: string;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(50)
  @ValidateNested({ each: true })
  @Type(() => ExpenseItemDto)
  items!: ExpenseItemDto[];
}

export class ExpenseListQueryDto {
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
