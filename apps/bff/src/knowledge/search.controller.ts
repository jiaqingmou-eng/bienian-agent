import { Body, Controller, HttpCode, Post, Req } from '@nestjs/common';
import type { Request } from 'express';
import { BackendClient } from '../backend/backend.client';
import { CurrentUserId } from '../common/current-user.decorator';
import { parseSearchDocuments } from './knowledge.contract';
import { SearchDocumentsDto } from './knowledge.dto';

/** POST 只执行读取，问题放请求正文，复用全局会话 / Origin 校验而不记录问题到 URL。 */
@Controller('api/knowledge')
export class KnowledgeSearchController {
  constructor(private readonly backend: BackendClient) {}

  @Post('search')
  @HttpCode(200)
  async search(
    @CurrentUserId() userId: string,
    @Body() dto: SearchDocumentsDto,
    @Req() req: Request,
  ) {
    const result = await this.backend.request<unknown>('/api/v1/knowledge/search', {
      userId,
      requestId: String(req.id),
      method: 'POST',
      body: dto,
    });
    return parseSearchDocuments(result);
  }
}
