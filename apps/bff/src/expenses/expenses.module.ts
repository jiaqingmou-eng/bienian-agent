import { Module } from '@nestjs/common';
import { BackendModule } from '../backend/backend.module';
import { ExpensesController } from './expenses.controller';
import { ExpensesService } from './expenses.service';

@Module({ imports: [BackendModule], controllers: [ExpensesController], providers: [ExpensesService] })
export class ExpensesModule {}
