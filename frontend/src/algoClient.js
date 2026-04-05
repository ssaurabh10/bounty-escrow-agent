(() => {
/**
 * Bounty Escrow Agent - AlgoSDK client aligned to the current contract ABI.
 *
 * This client loads smart_contracts/bounty_escrow/artifacts/contract.json at runtime
 * so every caller uses the same ABI artifact that the contract export produced.
 */

let ALGOD_TOKEN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
let ALGOD_SERVER = "http://localhost";
let ALGOD_PORT = 4001;

const KMD_TOKEN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const KMD_SERVER = "http://localhost";
const KMD_PORT = 4002;

/** Pera Connect `chainId` values (see @perawallet/connect README). */
const PERA_CHAIN_ID = {
  ALL: "4160",
  MAINNET: "416001",
  TESTNET: "416002",
  BETANET: "416003",
};

const TESTNET_ALGOD = {
  server: "https://testnet-api.algonode.cloud",
  port: "",
  token: "",
};

const STATUS = {
  OPEN: 0,
  ACCEPTED: 1,
  SUBMITTED: 2,
  APPROVED: 3,
  REJECTED: 4,
  DISPUTED: 5,
  RESOLVED_WORKER: 6,
  RESOLVED_CREATOR: 7,
  OPTED_OUT: 8,
  CANCELLED: 9,
};

const SCORE_START = 100;

const STATUS_LABEL = {
  0: "Open",
  1: "Accepted",
  2: "Work Submitted",
  3: "Approved",
  4: "Rejected",
  5: "In Dispute",
  6: "Worker Won",
  7: "Creator Won",
  8: "Opted Out",
  9: "Cancelled by creator",
};

const STATUS_COLOR = {
  0: "#4ade80",
  1: "#60a5fa",
  2: "#fbbf24",
  3: "#4ade80",
  4: "#f87171",
  5: "#fb923c",
  6: "#10b981",
  7: "#ef4444",
  8: "#9ca3af",
  9: "#78716c",
};

const CONTRACT_JSON_CANDIDATES = [
  "/smart_contracts/bounty_escrow/artifacts/contract.json",
  "../smart_contracts/bounty_escrow/artifacts/contract.json",
  "./smart_contracts/bounty_escrow/artifacts/contract.json",
];

let algodClient = null;
let METHODS = null;
let contractSpecPromise = null;

function getAlgodClient() {
  if (!algodClient) {
    algodClient = new algosdk.Algodv2(ALGOD_TOKEN, ALGOD_SERVER, ALGOD_PORT);
  }
  return algodClient;
}

/**
 * Point the client at a different Algod (e.g. TestNet). Resets the cached client.
 * @param {{ server?: string, port?: string|number, token?: string }} cfg
 */
function setAlgodConfig(cfg = {}) {
  if (cfg.token !== undefined) ALGOD_TOKEN = cfg.token;
  if (cfg.server !== undefined) ALGOD_SERVER = cfg.server;
  if (cfg.port !== undefined) ALGOD_PORT = cfg.port;
  algodClient = null;
  contractSpecPromise = null;
}

function applyTestnetAlgod() {
  setAlgodConfig({
    server: TESTNET_ALGOD.server,
    port: TESTNET_ALGOD.port,
    token: TESTNET_ALGOD.token,
  });
}

function applyLocalnetAlgod() {
  setAlgodConfig({
    server: "http://localhost",
    port: 4001,
    token: "a".repeat(64),
  });
}

/** @param {any[]} txns algosdk.Transaction[] */
async function signTransactionGroup(account, txns) {
  if (!account || !txns?.length) {
    throw new Error("signTransactionGroup: account and transactions are required");
  }
  if (account.type === "kmd" && account.sk) {
    return txns.map((txn) => txn.signTxn(account.sk));
  }
  if (account.type === "pera" && account.wallet && account.address) {
    const group = txns.map((txn) => ({ txn, signers: [] }));
    const signed = await account.wallet.signTransaction([group], account.address);
    const list = Array.isArray(signed) ? signed : [signed];
    return list.map((item) => {
      if (item instanceof Uint8Array) {
        return item;
      }
      if (item && item.blob instanceof Uint8Array) {
        return item.blob;
      }
      if (item && item.txn instanceof Uint8Array) {
        return item.txn;
      }
      throw new Error("Unexpected shape from Pera signTransaction; expected Uint8Array or { blob }");
    });
  }
  throw new Error("Account cannot sign: use wrapKmdAccount() or wrapPeraAccount()");
}

function wrapKmdAccount(entry) {
  if (!entry?.address || !entry.sk) {
    throw new Error("KMD account needs { address, sk }");
  }
  return { type: "kmd", address: entry.address, sk: entry.sk };
}

function wrapPeraAccount(peraWallet, address) {
  if (!peraWallet || !address) {
    throw new Error("Pera account needs wallet instance and address");
  }
  return { type: "pera", wallet: peraWallet, address };
}

async function getKMDAccounts() {
  const kmdClient = new algosdk.Kmd(KMD_TOKEN, KMD_SERVER, KMD_PORT);
  const wallets = await kmdClient.listWallets();
  const defaultWallet = wallets.wallets.find(
    (wallet) => wallet.name === "unencrypted-default-wallet",
  );

  if (!defaultWallet) {
    throw new Error("LocalNet wallet not found. Is LocalNet running?");
  }

  const { wallet_handle_token: handle } = await kmdClient.initWalletHandle(
    defaultWallet.id,
    "",
  );
  const { addresses } = await kmdClient.listKeys(handle);
  const accounts = [];

  for (const address of addresses) {
    const { private_key } = await kmdClient.exportKey(handle, "", address);
    accounts.push({ address, sk: private_key });
  }

  await kmdClient.releaseWalletHandle(handle);
  return accounts;
}

async function suggestedParams() {
  const params = await getAlgodClient().getTransactionParams().do();
  params.fee = 2000;
  params.flatFee = true;
  return params;
}

function methodSelector(signature) {
  return algosdk.ABIMethod.fromSignature(signature).getSelector();
}

function getMethodSignature(methodSpec) {
  const argTypes = (methodSpec.args || []).map((arg) => arg.type).join(",");
  const returnType = methodSpec.returns?.type || "void";
  return `${methodSpec.name}(${argTypes})${returnType}`;
}

async function loadContractSpec() {
  if (!contractSpecPromise) {
    contractSpecPromise = (async () => {
      const errors = [];
      for (const candidate of CONTRACT_JSON_CANDIDATES) {
        const url = new URL(candidate, window.location.href).href;
        const response = await fetch(url).catch((error) => {
          errors.push(`${candidate}: ${error?.message || error}`);
          return null;
        });
        if (!response) continue;
        if (!response.ok) {
          errors.push(`${candidate}: ${response.status} ${response.statusText}`);
          continue;
        }
        return response.json();
      }
      throw new Error(`Unable to load contract ABI. Tried: ${errors.join(" | ")}`);
    })();
  }
  return contractSpecPromise;
}

async function getMethodSignatures() {
  const contractSpec = await loadContractSpec();
  return Object.fromEntries(
    (contractSpec.methods || []).map((methodSpec) => [
      methodSpec.name,
      getMethodSignature(methodSpec),
    ]),
  );
}

async function getMethodSelectors() {
  if (!METHODS) {
    const methodSignatures = await getMethodSignatures();
    METHODS = Object.fromEntries(
      Object.entries(methodSignatures).map(([name, signature]) => [
        name,
        methodSelector(signature),
      ]),
    );
  }
  return METHODS;
}

function encodeString(value) {
  return algosdk.ABIType.from("string").encode(value);
}

function encodeUint64(value) {
  return algosdk.ABIType.from("uint64").encode(BigInt(value));
}

/** ARC-4: `account` and `pay` reference args are encoded as uint8 indices. */
function encodeUint8(value) {
  return algosdk.ABIType.from("uint8").encode(value);
}

function encodeBool(value) {
  return algosdk.ABIType.from("bool").encode(Boolean(value));
}

function encodeAddress(address) {
  // ABIType "address" encoder requires a 32-byte Uint8Array (raw public key),
  // NOT the human-readable 58-char Base32 string — decodeAddress extracts it.
  return algosdk.ABIType.from("address").encode(algosdk.decodeAddress(address).publicKey);
}

function normalizeAddress(address) {
  return typeof address === "string" ? address.trim() : "";
}

function isValidAddress(address) {
  const normalized = normalizeAddress(address);
  if (!normalized) return false;
  try {
    algosdk.decodeAddress(normalized);
    return true;
  } catch {
    return false;
  }
}

async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function scoreBoxName(address) {
  return algosdk.decodeAddress(address).publicKey;
}

function scoreBoxRef(appId, address) {
  return { appIndex: appId, name: scoreBoxName(address) };
}

function buildNoOpTxn({ from, appId, appArgs, accounts = [], boxes = [], foreignApps = [], foreignAssets = [] }, params) {
  // algosdk v2.x uses `from` (not `sender`) and `appIndex` (not `appId`)
  return algosdk.makeApplicationCallTxnFromObject({
    from,
    appIndex: appId,
    onComplete: algosdk.OnApplicationComplete.NoOpOC,
    appArgs,
    accounts,
    boxes,
    foreignApps,
    foreignAssets,
    suggestedParams: params,
  });
}

async function submitSignedTxns(signedTxns) {
  const client = getAlgodClient();
  const { txId } = await client.sendRawTransaction(signedTxns).do();
  await algosdk.waitForConfirmation(client, txId, 4);
  return txId;
}

async function loadParticipantState(appId) {
  const state = await getBountyState(appId);
  if (!state.creator || !state.contributor) {
    throw new Error("Contract state is missing creator or contributor account");
  }
  return state;
}

async function postBounty({
  appId,
  creatorAccount,
  rewardMicroAlgos,
  criteriaHash,
  testSuiteHash,
  deadlineUnix,
  arbitratorType = "auto",
  arbitratorAddress = creatorAccount.address,
}) {
  if (!Number.isInteger(appId) || appId <= 0) {
    throw new Error("App ID is invalid. Set a valid LocalNet App ID before posting.");
  }
  const creatorAddress = normalizeAddress(creatorAccount?.address);
  if (!creatorAddress) {
    throw new Error("Creator account is missing an address.");
  }
  if (!isValidAddress(creatorAddress)) {
    throw new Error("Creator account address is malformed.");
  }
  let resolvedArbitratorAddress =
    isValidAddress(arbitratorAddress) ? normalizeAddress(arbitratorAddress) : "";
  if (!resolvedArbitratorAddress) {
    const localnetAccounts = await getKMDAccounts().catch(() => []);
    resolvedArbitratorAddress = normalizeAddress(localnetAccounts[2]?.address || creatorAddress);
  }
  if (!isValidAddress(resolvedArbitratorAddress)) {
    throw new Error("Arbitrator address is required to post a bounty.");
  }
  if (arbitratorType !== "auto") {
    throw new Error("Only automated oracle disputes are supported.");
  }
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const appAddress = algosdk.getApplicationAddress(appId);

  // algosdk v2.x uses `from`/`to` (not `sender`/`receiver` which are v3 names)
  const paymentTxn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
    from: creatorAddress,
    to: appAddress,
    amount: rewardMicroAlgos,
    suggestedParams: params,
  });

  // Payment must be txn 0 in the group; ABI `pay` arg is the uint8 index into the atomic group.
  const appTxn = buildNoOpTxn(
    {
      from: creatorAddress,
      appId,
      appArgs: [
        methods.post_bounty,
        encodeString(criteriaHash),
        encodeString(testSuiteHash),
        encodeUint64(deadlineUnix),
        encodeString(arbitratorType),
        encodeAddress(resolvedArbitratorAddress),
        encodeUint8(0),
      ],
    },
    params,
  );

  algosdk.assignGroupID([paymentTxn, appTxn]);
  const signed = await signTransactionGroup(creatorAccount, [paymentTxn, appTxn]);
  return submitSignedTxns(signed);
}

async function acceptBounty({ appId, workerAccount }) {
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: workerAccount.address,
      appId,
      appArgs: [methods.accept_bounty],
    },
    params,
  );
  const signed = await signTransactionGroup(workerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function withdrawAcceptance({ appId, workerAccount }) {
  const methods = await getMethodSelectors();
  const state = await getBountyState(appId);
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: workerAccount.address,
      appId,
      appArgs: [methods.withdraw_acceptance],
      boxes: state.contributor ? [scoreBoxRef(appId, state.contributor)] : [],
    },
    params,
  );
  const signed = await signTransactionGroup(workerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function submitWork({ appId, workerAccount, ipfsHash, submissionHash }) {
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const frozenSubmissionHash = submissionHash || await sha256Hex(ipfsHash);
  const txn = buildNoOpTxn(
    {
      from: workerAccount.address,
      appId,
      appArgs: [methods.submit_work, encodeString(ipfsHash), encodeString(frozenSubmissionHash)],
    },
    params,
  );
  const signed = await signTransactionGroup(workerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function approveWork({ appId, creatorAccount }) {
  const methods = await getMethodSelectors();
  const state = await loadParticipantState(appId);
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: creatorAccount.address,
      appId,
      appArgs: [methods.approve_work, encodeUint8(0)],
      accounts: [state.contributor],
      boxes: [scoreBoxRef(appId, state.contributor)],
    },
    params,
  );
  const signed = await signTransactionGroup(creatorAccount, [txn]);
  return submitSignedTxns(signed);
}

async function rejectWork({ appId, creatorAccount }) {
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: creatorAccount.address,
      appId,
      appArgs: [methods.reject_work],
    },
    params,
  );
  const signed = await signTransactionGroup(creatorAccount, [txn]);
  return submitSignedTxns(signed);
}

async function reopenAfterRejection({ appId, creatorAccount }) {
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: creatorAccount.address,
      appId,
      appArgs: [methods.reopen_after_rejection],
    },
    params,
  );
  const signed = await signTransactionGroup(creatorAccount, [txn]);
  return submitSignedTxns(signed);
}

async function creatorCancelBounty({ appId, creatorAccount }) {
  const methods = await getMethodSelectors();
  const state = await getBountyState(appId);
  const params = await suggestedParams();
  const boxes =
    state.status === STATUS.ACCEPTED && state.contributor
      ? [scoreBoxRef(appId, state.contributor)]
      : [];
  const txn = buildNoOpTxn(
    {
      from: creatorAccount.address,
      appId,
      appArgs: [methods.creator_cancel_bounty, encodeUint8(0)],
      accounts: [creatorAccount.address],
      boxes,
    },
    params,
  );
  const signed = await signTransactionGroup(creatorAccount, [txn]);
  return submitSignedTxns(signed);
}

async function raiseDispute({ appId, workerAccount }) {
  const methods = await getMethodSelectors();
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: workerAccount.address,
      appId,
      appArgs: [methods.raise_dispute],
    },
    params,
  );
  const signed = await signTransactionGroup(workerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function optOut({ appId, workerAccount }) {
  const methods = await getMethodSelectors();
  const state = await loadParticipantState(appId);
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: workerAccount.address,
      appId,
      appArgs: [methods.opt_out, encodeUint8(0)],
      accounts: [state.creator],
      boxes: [scoreBoxRef(appId, state.contributor)],
    },
    params,
  );
  const signed = await signTransactionGroup(workerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function autoRelease({ appId, callerAccount }) {
  const methods = await getMethodSelectors();
  const state = await loadParticipantState(appId);
  const params = await suggestedParams();
  const txn = buildNoOpTxn(
    {
      from: callerAccount.address,
      appId,
      appArgs: [methods.auto_release_after_silence, encodeUint8(0)],
      accounts: [state.contributor],
      boxes: [scoreBoxRef(appId, state.contributor)],
    },
    params,
  );
  const signed = await signTransactionGroup(callerAccount, [txn]);
  return submitSignedTxns(signed);
}

async function oracleVerdict({
  appId,
  oracleAccount,
  verdict,
  oracleOutput = "",
  verdictReason,
  observedSubmissionHash,
}) {
  const methods = await getMethodSelectors();
  const state = await loadParticipantState(appId);
  const params = await suggestedParams();
  const frozenSubmissionHash = observedSubmissionHash || state.submissionHash;
  const verdictText = verdictReason || `Oracle verdict: ${verdict}`;
  const oracleOutputHash = await sha256Hex(oracleOutput);
  const verdictReasonHash = await sha256Hex(verdictText);
  const txn = buildNoOpTxn(
    {
      from: oracleAccount.address,
      appId,
      appArgs: [
        methods.oracle_verdict,
        encodeString(verdict),
        encodeString(frozenSubmissionHash),
        encodeString(oracleOutputHash),
        encodeString(verdictReasonHash),
        encodeUint8(0),
        encodeUint8(1),
      ],
      accounts: [state.creator, state.contributor],
      boxes: [scoreBoxRef(appId, state.contributor)],
    },
    params,
  );
  const signed = await signTransactionGroup(oracleAccount, [txn]);
  return submitSignedTxns(signed);
}

function decodeUint64Bytes(bytes) {
  const normalized = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (normalized.length !== 8) {
    throw new Error(`Expected 8-byte uint64 box value, received ${normalized.length} bytes`);
  }
  const view = new DataView(normalized.buffer, normalized.byteOffset, normalized.byteLength);
  return Number(view.getBigUint64(0, false));
}

function decodeStateKey(keyBase64) {
  if (typeof keyBase64 !== "string") {
    return "";
  }
  try {
    return new TextDecoder().decode(
      Uint8Array.from(atob(keyBase64), (char) => char.charCodeAt(0)),
    );
  } catch {
    return keyBase64;
  }
}

/** Normalize Algod application responses (shape varies by client / version). */
function readApplicationGlobalState(info) {
  if (!info || typeof info !== "object") {
    return [];
  }
  const direct = info["global-state"] ?? info.globalState;
  if (Array.isArray(direct)) {
    return direct;
  }
  const paramSources = [
    info.params,
    info.application?.params,
    info.Application?.params,
  ].filter(Boolean);
  for (const p of paramSources) {
    const gs = p["global-state"] ?? p.globalState;
    if (Array.isArray(gs)) {
      return gs;
    }
  }
  return [];
}

function decodeStateBytes(bytesBase64) {
  return Uint8Array.from(atob(bytesBase64 || ""), (char) => char.charCodeAt(0));
}

function decodeAddress(bytes) {
  try {
    return bytes.length === 32 ? algosdk.encodeAddress(bytes) : "";
  } catch {
    return "";
  }
}

/** Global zero address (e.g. after reopen_after_rejection) is not an assigned worker. */
function isZeroAlgorandAddress(address) {
  if (!address || typeof address !== "string") return true;
  try {
    return algosdk.decodeAddress(address).publicKey.every((b) => b === 0);
  } catch {
    return true;
  }
}

function decodeString(bytes) {
  try {
    return bytes.length ? new TextDecoder().decode(bytes) : "";
  } catch {
    return "";
  }
}

async function getBountyState(appId) {
  const client = getAlgodClient();
  const info = await client.getApplicationByID(appId).do();
  const state = {};

  for (const kv of readApplicationGlobalState(info)) {
    const key = decodeStateKey(kv.key);
    if (!key) {
      continue;
    }
    const value = kv.value;
    if (!value) {
      continue;
    }
    const isBytes = value.type === 1 || value.type === "1";
    if (isBytes) {
      state[key] = decodeStateBytes(value.bytes);
    } else {
      const u = value.uint;
      state[key] = typeof u === "bigint" ? Number(u) : u;
    }
  }

  const status = state.status || 0;

  const contributorAddr = decodeAddress(state.contributor || new Uint8Array());

  return {
    creator: decodeAddress(state.creator || new Uint8Array()),
    contributor: isZeroAlgorandAddress(contributorAddr) ? "" : contributorAddr,
    rewardAmount: state.reward_amount || 0,
    deadline: state.deadline || 0,
    criteriaHash: decodeString(state.criteria_hash || new Uint8Array()),
    testSuiteHash: decodeString(state.test_suite_hash || new Uint8Array()),
    workIpfsHash: decodeString(state.work_ipfs_hash || new Uint8Array()),
    submissionHash: decodeString(state.submission_hash || new Uint8Array()),
    arbitratorType: decodeString(state.arbitrator_type || new Uint8Array()) || "auto",
    arbitratorAddr: decodeAddress(state.arbitrator_addr || new Uint8Array()),
    verdictCode: decodeString(state.verdict_code || new Uint8Array()),
    oracleOutputHash: decodeString(state.oracle_output_hash || new Uint8Array()),
    verdictReasonHash: decodeString(state.verdict_reason_hash || new Uint8Array()),
    oracleVerdictAt: state.oracle_verdict_at || 0,
    status,
    statusLabel: STATUS_LABEL[status] || `Unknown (${status})`,
    statusColor: STATUS_COLOR[status] || "#888888",
    submittedAt: state.submitted_at || 0,
    rejectedAt: state.rejected_at || 0,
  };
}

async function getAccountBalance(address) {
  const info = await getAlgodClient().accountInformation(address).do();
  return info.amount / 1_000_000;
}

async function getCreditScore(appId, address) {
  try {
    const response = await getAlgodClient().getApplicationBoxByName(appId, scoreBoxName(address)).do();
    return decodeUint64Bytes(response.value);
  } catch (error) {
    const message = String(error?.message || error);
    if (message.includes("box not found") || message.includes("404")) {
      return SCORE_START;
    }
    throw error;
  }
}

const algoClient = {
  STATUS,
  STATUS_LABEL,
  STATUS_COLOR,
  SCORE_START,
  PERA_CHAIN_ID,
  setAlgodConfig,
  applyLocalnetAlgod,
  applyTestnetAlgod,
  wrapKmdAccount,
  wrapPeraAccount,
  signTransactionGroup,
  loadContractSpec,
  getMethodSignatures,
  getAlgodClient,
  getKMDAccounts,
  getBountyState,
  getAccountBalance,
  getCreditScore,
  postBounty,
  acceptBounty,
  withdrawAcceptance,
  submitWork,
  approveWork,
  rejectWork,
  reopenAfterRejection,
  creatorCancelBounty,
  raiseDispute,
  optOut,
  autoRelease,
  oracleVerdict,
};

if (typeof window !== "undefined") {
  window.algoClient = algoClient;
}
})();
