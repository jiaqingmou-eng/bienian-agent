import { Module } from '@nestjs/common';
import { BackendModule } from '../backend/backend.module';
import { AgentController } from './agent.controller';
import { ChatController } from './chat.controller';

@Module({ imports: [BackendModule], controllers: [AgentController, ChatController] })
export class AgentModule {}
