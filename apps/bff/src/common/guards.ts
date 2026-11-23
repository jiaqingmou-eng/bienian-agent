import { CanActivate, ExecutionContext, ForbiddenException, Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import { PUBLIC_ROUTE } from './public.decorator';

@Injectable()
export class OriginGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest<Request>();
    // Cookie-based authentication requires CSRF protection even on demo-login/logout.
    if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method) &&
        req.headers.origin !== this.config.getOrThrow<string>('APP_ORIGIN')) {
      throw new ForbiddenException('请求来源不被允许');
    }
    return true;
  }
}

@Injectable()
export class SessionGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_ROUTE, [
      context.getHandler(), context.getClass(),
    ]);
    if (isPublic) return true;
    const req = context.switchToHttp().getRequest<Request>();
    if (!req.session?.actorId) throw new UnauthorizedException('请先登录');
    return true;
  }
}
