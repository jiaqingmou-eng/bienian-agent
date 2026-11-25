import {
  BadRequestException,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Query,
  Req,
} from '@nestjs/common';
import type { Request } from 'express';
import { BackendClient } from '../backend/backend.client';
import { CurrentUserId } from '../common/current-user.decorator';
import { parseCitationDetail, parseDocumentDetail, parseDocumentList } from './knowledge.contract';
import { CitationParamsDto, DocumentListQueryDto } from './knowledge.dto';

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

  /** GET /api/knowledge/documents/:id/citations/:citation_id：读取固定版本的真实条款。
   * 路径由DTO校验；引用读取不接收查询参数，避免客户端行号 / 身份混入定位依据。
   * 只转发可信会话身份和请求编号，Python负责授权、快照及版本核对。
   * 404 / 503沿用后端错误语义；不调用模型，不自动重试或回退到其他文档。
   */
  @Get(':id/citations/:citation_id')
  async citation(
    @CurrentUserId() userId: string,
    @Param() params: CitationParamsDto,
    @Query() query: Record<string, unknown>,
    @Req() req: Request,
  ) {
    if (Object.keys(query).length > 0) {
      throw new BadRequestException('引用读取不接受查询参数');
    }
    const result = await this.backend.request<unknown>(
      `/api/v1/knowledge/documents/${params.id}/citations/${params.citation_id}`,
      { userId, requestId: String(req.id) },
    );
    // 除结构外同时核对路径目标，防止上游误返回其他文档或引用时被展示为当前依据。
    return parseCitationDetail(result, params.id, params.citation_id);
  }
}
