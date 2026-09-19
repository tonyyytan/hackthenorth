using System;
using UnityEngine;

namespace HackTheNorth.LLM
{
    /// <summary>
    /// Placeholder ILlmClient that echoes a canned response so the pipeline is testable
    /// end-to-end before a real API key/backend exists. Swap Query's body for a real call.
    /// </summary>
    public class StubLlmClient : MonoBehaviour, ILlmClient
    {
        public void Query(string prompt, Action<string> onResult)
        {
            Debug.Log($"StubLlmClient: query \"{prompt}\" (no real LLM wired up yet).");
            onResult?.Invoke($"[stub response for: {prompt}]");
        }
    }
}
