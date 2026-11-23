import { createParamDecorator, ExecutionContext, UnauthorizedException } from '@nestjs/common';
import type { Request } from 'express';

export const CurrentUserId = createParamDecorator((_data: unknown, context: ExecutionContext) => {
  const userId = context.switchToHttp().getRequest<Request>().session?.actorId;
  if (!userId) throw new UnauthorizedException('请先登录');
  return userId;
});
