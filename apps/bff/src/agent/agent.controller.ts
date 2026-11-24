import {
  Body,
  Controller,
  Get,
  HttpCode,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
} from '@nestjs/common';
import type { Request } from 'express';
import { BackendClient } from '../backend/backend.client';
import { CurrentUserId } from '../common/current-user.decorator';
import { parseAgentRun, parseAgentRunList } from './agent.contract';
import {
  AgentConfirmDto,
  AgentMessageDto,
  AgentRunListQueryDto,
  AgentVersionDto,
  StartAgentRunDto,
} from './agent.dto';

@Controller('api/agent/runs')
export class AgentController {
  constructor(private readonly backend: BackendClient) {}

  @Get()
  async list(
    @Query() query: AgentRunListQueryDto,
    @CurrentUserId() userId: string,
    @Req() req: Request,
  ) {
    const search = new URLSearchParams({
      limit: String(query.limit),
      offset: String(query.offset),
    });
    return parseAgentRunList(
      await this.backend.request<unknown>(`/api/v1/agent/runs?${search}`, {
        userId,
        requestId: String(req.id),
      }),
    );
  }

  private async forward(path: string, userId: string, requestId: string, body?: unknown) {
    const result = await this.backend.request<unknown>(`/api/v1/agent/runs${path}`, {
      userId,
      requestId,
      method: body === undefined ? 'GET' : 'POST',
      body,
      // Allow synchronous model extraction plus validation and persistence; never retry writes here.
      timeoutMs: 60_000,
    });
    return parseAgentRun(result);
  }

  @Post()
  start(@Body() body: StartAgentRunDto, @CurrentUserId() userId: string, @Req() req: Request) {
    return this.forward('', userId, String(req.id), body);
  }

  @Get(':id')
  get(
    @Param('id', new ParseUUIDPipe()) id: string,
    @CurrentUserId() userId: string,
    @Req() req: Request,
  ) {
    return this.forward(`/${id}`, userId, String(req.id));
  }

  @Post(':id/messages')
  @HttpCode(200)
  message(
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: AgentMessageDto,
    @CurrentUserId() userId: string,
    @Req() req: Request,
  ) {
    return this.forward(`/${id}/messages`, userId, String(req.id), body);
  }

  @Post(':id/confirm')
  @HttpCode(200)
  confirm(
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: AgentConfirmDto,
    @CurrentUserId() userId: string,
    @Req() req: Request,
  ) {
    return this.forward(`/${id}/confirm`, userId, String(req.id), body);
  }

  @Post(':id/cancel')
  @HttpCode(200)
  cancel(
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: AgentVersionDto,
    @CurrentUserId() userId: string,
    @Req() req: Request,
  ) {
    return this.forward(`/${id}/cancel`, userId, String(req.id), body);
  }
}
