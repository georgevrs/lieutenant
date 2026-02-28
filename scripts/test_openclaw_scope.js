const WebSocket = require('ws');
const ws = new WebSocket('ws://127.0.0.1:18789/ws');

ws.on('open', () => console.log('connected'));

ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  console.log('<<<', JSON.stringify(msg, null, 2));
  
  if (msg.event === 'connect.challenge') {
    const connect = {
      type: 'req',
      id: 'test-1',
      method: 'connect',
      params: {
        minProtocol: 3,
        maxProtocol: 3,
        client: {
          id: 'gateway-client',
          displayName: 'Lieutenant',
          version: '0.1.0',
          platform: 'darwin',
          mode: 'backend',
        },
        caps: [],
        auth: { token: 'ea2638236c394cc7c6f0e030aba10a5e0cc0378baffef2a9' },
        role: 'operator',
        scopes: ['operator.admin', 'operator.read', 'operator.write', 'operator.approvals', 'operator.pairing'],
      },
    };
    console.log('>>> CONNECT:', JSON.stringify(connect));
    ws.send(JSON.stringify(connect));
  }
  
  if (msg.id === 'test-1') {
    if (msg.ok) {
      console.log('=== CONNECTED OK ===');
      console.log('payload:', JSON.stringify(msg.payload, null, 2));
      
      const agentReq = {
        type: 'req',
        id: 'test-2',
        method: 'agent',
        params: {
          message: 'Γεια σου',
          agentId: 'main',
          idempotencyKey: 'lt-test-' + Date.now(),
          extraSystemPrompt: 'Είσαι ο Υπολοχαγός.',
        },
      };
      console.log('>>> AGENT:', JSON.stringify(agentReq));
      ws.send(JSON.stringify(agentReq));
    } else {
      console.log('=== CONNECT FAILED ===', msg.error);
      ws.close();
    }
  }
  
  if (msg.id === 'test-2') {
    if (!msg.ok && msg.error) {
      console.log('=== AGENT ERROR ===', JSON.stringify(msg.error));
      ws.close();
    }
  }
  
  if (msg.event === 'chat' && msg.payload && msg.payload.state === 'final') {
    console.log('=== DONE ===');
    ws.close();
  }
});

ws.on('error', (e) => console.error('WS error:', e.message));
ws.on('close', () => { console.log('closed'); process.exit(0); });
setTimeout(() => { console.log('timeout'); process.exit(1); }, 15000);
