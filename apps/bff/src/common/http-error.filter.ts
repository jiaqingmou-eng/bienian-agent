import { ArgumentsHost, Catch, ExceptionFilter, HttpException } from '@nestjs/common';
import type { Request, Response } from 'express';

@Catch()
export class HttpErrorFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost): void {
    const request = host.switchToHttp().getRequest<Request>();
    const response = host.switchToHttp().getResponse<Response>();
    const status = exception instanceof HttpException ? exception.getStatus() : 500;
    const payload = exception instanceof HttpException ? exception.getResponse() : undefined;
    const raw = typeof payload === 'object' && payload !== null && 'message' in payload
      ? payload.message : payload;
    response.status(status).json({
      code: `HTTP_${status}`,
      message: typeof raw === 'string' ? raw : Array.isArray(raw) ? raw.join('；') : '服务暂不可用',
      requestId: request.id,
    });
  }
}
