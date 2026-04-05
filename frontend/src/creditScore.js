/**
 * Credit Score helpers for the on-chain reputation model.
 *
 * Reputation is stored in Algorand application boxes keyed by contributor address.
 * `window.algoClient.getCreditScore(appId, address)` is the runtime source of truth.
 */

const SCORE_START = 100;
const SCORE_APPROVED = 50;
const SCORE_AUTO = 20;
const SCORE_DISPUTE_WIN = 30;
const SCORE_DISPUTE_LOSS = -80;
const SCORE_OPT_OUT = -10;
const SCORE_EXPIRE = -30;

function getScoreTier(score) {
  if (score >= 800) return { tier: "Gold", color: "#FFD700", minScore: 800 };
  if (score >= 500) return { tier: "Silver", color: "#C0C0C0", minScore: 500 };
  if (score >= 200) return { tier: "Bronze", color: "#CD7F32", minScore: 200 };
  return { tier: "Open", color: "#888888", minScore: 0 };
}

function canAccessBounty(contributorScore, minimumTierRequired) {
  const tierMinScores = { Open: 0, Bronze: 200, Silver: 500, Gold: 800 };
  return contributorScore >= (tierMinScores[minimumTierRequired] || 0);
}

class CreditScoreStore {
  static async getScore(appId, address) {
    if (!window.algoClient) {
      throw new Error("algoClient is not loaded");
    }
    return window.algoClient.getCreditScore(appId, address);
  }

  static async getLeaderboard(appId, addresses) {
    const uniqueAddresses = [...new Set(addresses.filter(Boolean))];
    const scores = await Promise.all(
      uniqueAddresses.map(async (address) => ({
        address,
        score: await this.getScore(appId, address),
      })),
    );

    return scores
      .map((entry) => ({ ...entry, tierInfo: getScoreTier(entry.score) }))
      .sort((a, b) => b.score - a.score);
  }
}

if (typeof window !== "undefined") {
  window.creditScore = {
    SCORE_START,
    SCORE_APPROVED,
    SCORE_AUTO,
    SCORE_DISPUTE_WIN,
    SCORE_DISPUTE_LOSS,
    SCORE_OPT_OUT,
    SCORE_EXPIRE,
    getScoreTier,
    canAccessBounty,
    CreditScoreStore,
  };
}
