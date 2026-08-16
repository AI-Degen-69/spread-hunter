"""Query on-chain Polygon balances and allowances for Polymarket addresses."""
import httpx

RPC = 'https://polygon.drpc.org'

ADDRESSES = {
    'Signer EOA': '0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0',
    'Proxy (Safe/Gnosis)': '0xBa7c21Ac8968983e90BEcB989fe978889FEC266b',
    'Deposit Address': '0xF495052dA3a06eB189f6619e8eE197fe5EdC4c82'
}

NATIVE_USDC = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'
BRIDGED_USDCE = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'

ALLOWANCE_TARGETS = {
    'CTF Exchange (Main)': '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E',
    'Old Exchange': '0xE111180000d2663C0091e4f400237545B87B996B',
    'Neg Risk CTF Exchange': '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296',
    'Neg Risk Adapter': '0xe2222d279d744050d28e00520010520000310F59'
}

def rpc_call(client, method, params):
    resp = client.post(RPC, json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
    data = resp.json()
    if 'result' in data:
        return data['result']
    raise RuntimeError(f"RPC error: {data}")

def main():
    print('=== POLYGON ON-CHAIN AUDIT ===\n')
    with httpx.Client(timeout=10.0) as client:
        for name, addr in ADDRESSES.items():
            pol_hex = rpc_call(client, 'eth_getBalance', [addr, 'latest'])
            pol = int(pol_hex, 16) / 1e18
            
            code = rpc_call(client, 'eth_getCode', [addr, 'latest'])
            is_contract = code != '0x' and len(code) > 2
            
            # Balances
            data_bal = '0x70a08231' + addr[2:].lower().zfill(64)
            native_hex = rpc_call(client, 'eth_call', [{'to': NATIVE_USDC, 'data': data_bal}, 'latest'])
            usdce_hex = rpc_call(client, 'eth_call', [{'to': BRIDGED_USDCE, 'data': data_bal}, 'latest'])
            
            native_bal = int(native_hex, 16) / 1e6
            usdce_bal = int(usdce_hex, 16) / 1e6
            
            print(f"[{name}] {addr}")
            print(f"  Bytecode:        {'Contract (length ' + str(len(code)) + ')' if is_contract else 'EOA'}")
            print(f"  POL Balance:     {pol:.6f} POL")
            print(f"  Native USDC:     ${native_bal:.6f}")
            print(f"  Bridged USDC.e:  ${usdce_bal:.6f}")
            
            # Allowances
            print(f"  Allowances (USDC.e):")
            for tname, target in ALLOWANCE_TARGETS.items():
                data_allow = '0xdd62ed3e' + addr[2:].lower().zfill(64) + target[2:].lower().zfill(64)
                allow_usdce_hex = rpc_call(client, 'eth_call', [{'to': BRIDGED_USDCE, 'data': data_allow}, 'latest'])
                allow_usdce = int(allow_usdce_hex, 16) / 1e6
                print(f"    -> {tname} ({target[:10]}...): ${allow_usdce:.2f}")
            print()

if __name__ == '__main__':
    main()
