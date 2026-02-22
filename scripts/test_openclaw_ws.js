const WebSocket = require("ws");
const crypto = require("crypto");
const TOKEN = "ea2638236c394cc7c6f0e030aba10a5e0cc0378baffef2a9";

const ws = new WebSocket("ws://127.0.0.1:18789/ws");
let msgCount = 0;
let connected = false;

ws.on("open", () => console.log("WS connected"));

ws.on("message", (data) => {
    const raw = data.toString();
    let msg;
    try { msg = JSON.parse(raw); } catch { console.log("<< RAW:", raw.substring(0, 300)); return; }
    
    const display = JSON.stringify(msg).substring(0, 800);
    console.log("<<", display);
    msgCount++;

    // Step 1: Respond to challenge
    if (msg.type === "event" && msg.event === "connect.challenge") {
        const connectReq = {
            type: "req",
            id: crypto.randomUUID(),
            method: "connect",
            params: {
                minProtocol: 3,
                maxProtocol: 3,
                client: {
                    id: "gateway-client",
                    displayName: "Lieutenant",
                    version: "0.1.0",
                    platform: "darwin",
                    mode: "backend"
                },
                caps: [],
                auth: { token: TOKEN },
                role: "operator",
                scopes: ["operator.admin"]
            }
        };
        console.log(">> connect");
        ws.send(JSON.stringify(connectReq));
    }

    // Step 2: On connect OK, try agent method
    if (msg.type === "res" && msg.ok === true && !connected) {
        connected = true;
        console.log("CONNECTED!");
        
        const agentReq = {
            type: "req",
            id: crypto.randomUUID(),
            method: "agent",
            params: {
                message: "Πες γεια σε μία λέξη",
                agentId: "main",
                idempotencyKey: crypto.randomUUID()
            }
        };
        console.log(">> agent", JSON.stringify(agentReq.params));
        ws.send(JSON.stringify(agentReq));
    }

    if (msgCount > 50) { ws.close(); process.exit(0); }
});

ws.on("error", (e) => { console.log("ERR:", e.message); process.exit(1); });
ws.on("close", (code) => { console.log("CLOSED", code); process.exit(0); });
setTimeout(() => { console.log("TIMEOUT"); ws.close(); process.exit(0); }, 25000);
