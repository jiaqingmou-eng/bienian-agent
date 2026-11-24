import { Module } from '@nestjs/common';
import { BackendModule } from '../backend/backend.module';
import { KnowledgeController } from './knowledge.controller';
import { KnowledgeSearchController } from './search.controller';

/** 本次只注册制度只读入口，不注册上传、发布或 Agent 写工具。 */
@Module({ imports: [BackendModule], controllers: [KnowledgeController, KnowledgeSearchController] })
export class KnowledgeModule {}
