import { Controller, Get, Param, ParseUUIDPipe, Query, Req } from '@nestjs/common';
import type { Request } from 'express';
import { BackendClient } from '../backend/backend.client';
import { CurrentUserId } from '../common/current-user.decorator';
import { parseDocumentDetail, parseDocumentList } from './knowledge.contract';
import { DocumentListQueryDto } from './knowledge.dto';

/** 复用全局会话守卫，只传可信会话用户；具体文档授权始终由 Python 决定。 */
@Controller('api/knowledge/documents')
export class KnowledgeController {
  constructor(private readonly backend: BackendClient) {}

  @Get()
  async list(
    @CurrentUserId() userId: string,
    @Query() query: DocumentListQueryDto,
    @Req() req: Request,
  ) {
    const params = new URLSearchParams({
      limit: String(query.limit),
      offset: String(query.offset),
    });
    const result = await this.backend.request<unknown>(`/api/v1/knowledge/documents?${params}`, {
      userId,
      requestId: String(req.id),
    });
    return parseDocumentList(result);
  }

  @Get(':id')
  async detail(
    @CurrentUserId() userId: string,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Req() req: Request,
  ) {
    const result = await this.backend.request<unknown>(`/api/v1/knowledge/documents/${id}`, {
      userId,
      requestId: String(req.id),
    });
    return parseDocumentDetail(result);
  }
}
