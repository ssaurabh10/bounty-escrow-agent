/**
 * IPFS Storage Utility — Bounty Escrow Agent (LocalNet Edition)
 * 
 * For LocalNet: Uses localStorage as a mock IPFS gateway.
 * SHA-256 hashing is real — used for on-chain verification.
 * 
 * In production, swap LocalIPFS with Infura/Pinata IPFS client.
 */


// ── SHA-256 Hashing (uses Web Crypto API — works in all browsers) ────────────

async function sha256(content) {
  const encoder = new TextEncoder();
  const data = encoder.encode(content);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
}

async function sha256File(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async (e) => {
      const hashBuffer = await crypto.subtle.digest("SHA-256", e.target.result);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      resolve(hashArray.map(b => b.toString(16).padStart(2, "0")).join(""));
    };
    reader.onerror = reject;
    reader.readAsArrayBuffer(file);
  });
}


// ── LocalIPFS (Mock IPFS for LocalNet) ────────────────────────────────────────

const IPFS_STORE_KEY = "bounty_escrow_ipfs_store";

class LocalIPFS {
  /**
   * Simulates IPFS using localStorage.
   * CID = "Qm" + first 44 chars of SHA-256 hash (mimics IPFS CID format)
   */

  static _getStore() {
    try {
      return JSON.parse(localStorage.getItem(IPFS_STORE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  static _saveStore(store) {
    localStorage.setItem(IPFS_STORE_KEY, JSON.stringify(store));
  }

  /**
   * "Upload" content to local IPFS store.
   * Returns { cid, hash, url, content }
   */
  static async add(content, filename = "file.txt") {
    const hash = await sha256(content);
    const cid = "Qm" + hash.substring(0, 44);

    const store = this._getStore();
    store[cid] = {
      content: content,
      filename: filename,
      hash: hash,
      timestamp: Date.now(),
    };
    this._saveStore(store);

    console.log(`📦 LocalIPFS: Stored "${filename}" → CID: ${cid}`);

    return {
      cid: cid,
      hash: hash,
      url: `local://ipfs/${cid}`,
      content: content,
    };
  }

  /**
   * "Download" content from local IPFS store.
   */
  static get(cid) {
    const store = this._getStore();
    const entry = store[cid];
    if (!entry) {
      console.warn(`📦 LocalIPFS: CID not found: ${cid}`);
      return null;
    }
    return entry;
  }

  /**
   * List all stored items.
   */
  static list() {
    return this._getStore();
  }

  /**
   * Clear all stored items.
   */
  static clear() {
    localStorage.removeItem(IPFS_STORE_KEY);
  }
}


// ── High-Level Upload Functions ───────────────────────────────────────────────

/**
 * Upload a file to local IPFS and return { cid, hash, url }
 */
async function uploadFileToIPFS(file) {
  const content = await file.text();
  const result = await LocalIPFS.add(content, file.name);
  return result;
}

/**
 * Upload text content to local IPFS
 */
async function uploadTextToIPFS(text, filename = "criteria.txt") {
  return await LocalIPFS.add(text, filename);
}

/**
 * Upload test suite file — returns hash to freeze on-chain
 */
async function uploadTestSuite(file) {
  const result = await uploadFileToIPFS(file);
  return {
    ...result,
    frozen_hash: result.hash,  // This hash gets stored in contract at creation
  };
}

/**
 * Verify content matches expected hash (local verification)
 */
async function verifyContent(cid, expectedHash) {
  const entry = LocalIPFS.get(cid);
  if (!entry) {
    return { valid: false, reason: "CID not found in local store" };
  }
  const actualHash = await sha256(entry.content);
  return {
    valid: actualHash === expectedHash,
    actualHash,
    expectedHash,
  };
}
