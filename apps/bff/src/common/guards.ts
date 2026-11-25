import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import { PUBLIC_ROUTE } from './public.decorator';

@Injectable()
export class OriginGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest<Request>();
    // 演示登录和退出也使用 Cookie，所有有副作用的请求都必须验证来源。
    if (
      !['GET', 'HEAD', 'OPTIONS'].includes(req.method) &&
      !this.isAllowedOrigin(req.headers.origin)
    ) {
      throw new ForbiddenException('请求来源不被允许');
    }
    return true;
  }

  private isAllowedOrigin(origin: string | undefined): boolean {
    const configured = this.config.getOrThrow<string>('APP_ORIGIN');
    if (origin === configured) return true;
    // Vite 默认显示 127.0.0.1，而调试入口使用 localhost。本地开发演示只兼容这两个
    // 明确的回环别名，不根据 Host / Referer 放行，也不把所有本机端口视为可信。
    if (
      !origin ||
      this.config.get<string>('NODE_ENV') !== 'development' ||
      this.config.get<string>('ENABLE_DEMO_LOGIN') !== 'true'
    ) {
      return false;
    }
    try {
      const incoming = new URL(origin);
      const expected = new URL(configured);
      const loopbackHosts = ['localhost', '127.0.0.1'];
      return (
        // Origin 应为规范的“协议 + 主机 + 端口”，带路径、用户信息或伪装主机均拒绝。
        incoming.origin === origin &&
        loopbackHosts.includes(incoming.hostname) &&
        loopbackHosts.includes(expected.hostname) &&
        incoming.protocol === expected.protocol &&
        incoming.port === expected.port
      );
    } catch {
      return false;
    }
  }
}

@Injectable()
export class SessionGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_ROUTE, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;
    const req = context.switchToHttp().getRequest<Request>();
    if (!req.session?.actorId) throw new UnauthorizedException('请先登录');
    return true;
  }
}
