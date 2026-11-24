import { Module } from '@nestjs/common';
import { BackendModule } from '../backend/backend.module';
import { AgentController } from './agent.controller';

@Module({ imports: [BackendModule], controllers: [AgentController] })
export class AgentModule {}
