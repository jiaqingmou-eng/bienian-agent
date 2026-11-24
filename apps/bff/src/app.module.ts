import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { APP_GUARD } from '@nestjs/core';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { LoggerModule } from 'nestjs-pino';
import { AuthModule } from './auth/auth.module';
import { BackendModule } from './backend/backend.module';
import { OriginGuard, SessionGuard } from './common/guards';
import { validateEnvironment } from './config/environment';
import { ExpensesModule } from './expenses/expenses.module';
import { HealthController } from './health.controller';
import { AgentModule } from './agent/agent.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: [resolve(process.cwd(), '.env'), resolve(__dirname, '../../../.env')],
      validate: validateEnvironment,
    }),
    LoggerModule.forRoot({
      pinoHttp: {
        genReqId: (_req, res) => {
          const id = randomUUID();
          res.setHeader('X-Request-Id', id);
          return id;
        },
        // Log operational metadata only; never serialize financial bodies or tokens.
        serializers: {
          req: (req: { id: string; method: string; url: string }) => ({
            id: req.id,
            method: req.method,
            path: req.url?.split('?')[0],
          }),
          res: (res: { statusCode: number }) => ({ statusCode: res.statusCode }),
          err: (err: { type?: string; message?: string }) => ({
            type: err.type,
            message: err.message,
          }),
        },
      },
    }),
    BackendModule,
    AuthModule,
    ExpensesModule,
    AgentModule,
  ],
  controllers: [HealthController],
  providers: [
    { provide: APP_GUARD, useClass: OriginGuard },
    { provide: APP_GUARD, useClass: SessionGuard },
  ],
})
export class AppModule {}
