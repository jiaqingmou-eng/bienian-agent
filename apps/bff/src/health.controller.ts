import { Controller, Get } from '@nestjs/common';
import { BackendClient } from './backend/backend.client';
import { Public } from './common/public.decorator';

@Public()
@Controller('api')
export class HealthController {
  constructor(private readonly backend: BackendClient) {}

  @Get('health')
  health() { return { status: 'ok', service: 'bff' }; }

  @Get('ready')
  async ready() {
    await this.backend.ready();
    return { status: 'ready' };
  }
}
