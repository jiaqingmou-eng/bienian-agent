import { Body, Controller, HttpCode, Post, Req } from '@nestjs/common';
import type { Request } from 'express';
import { BackendClient } from '../backend/backend.client';
import { CurrentUserId } from '../common/current-user.decorator';
import { parseChatReply } from './chat.contract';
import { ChatDto } from './chat.dto';

/** 统一自然语言入口；既有全局 session / Origin 守卫负责登录和跨站请求校验。 */
@Controller('api/agent')
export class ChatController {
  constructor(private readonly backend: BackendClient) {}

  /** POST /api/agent/chat：只转发校验过的对话参数和会话身份，不在 BFF 执行业务写入。
   * 后端先分类再分流；最多两次模型调用，沿用模型配置允许的60秒单次上限预留总超时。
   * 不自动重试，报销分支以 message_id 复用已有幂等语义；确认按钮继续走专用接口。
   */
  @Post('chat')
  @HttpCode(200)
  async converse(@Body() body: ChatDto, @CurrentUserId() userId: string, @Req() req: Request) {
    return parseChatReply(
      await this.backend.request<unknown>('/api/v1/agent/chat', {
        userId,
        requestId: String(req.id),
        method: 'POST',
        body,
        timeoutMs: 135_000,
      }),
    );
  }
}
