import { BadRequestException, Body, Controller, Get, Headers, Param, ParseUUIDPipe, Post, Query, Req } from '@nestjs/common';
import { isUUID } from 'class-validator';
import type { Request } from 'express';
import { CurrentUserId } from '../common/current-user.decorator';
import { CreateExpenseDto, ExpenseListQueryDto } from './expenses.dto';
import { ExpensesService } from './expenses.service';

@Controller('api/expenses')
export class ExpensesController {
  constructor(private readonly expenses: ExpensesService) {}

  @Get()
  list(@CurrentUserId() userId: string, @Query() query: ExpenseListQueryDto, @Req() req: Request) {
    return this.expenses.list(userId, query, String(req.id));
  }

  @Get(':id')
  detail(@CurrentUserId() userId: string, @Param('id', new ParseUUIDPipe()) id: string, @Req() req: Request) {
    return this.expenses.detail(userId, id, String(req.id));
  }

  @Post()
  create(
    @CurrentUserId() userId: string,
    @Body() dto: CreateExpenseDto,
    @Headers('idempotency-key') key: string | undefined,
    @Req() req: Request,
  ) {
    if (!key || !isUUID(key)) throw new BadRequestException('Idempotency-Key 必须为有效 UUID');
    return this.expenses.create(userId, dto, key, String(req.id));
  }
}
