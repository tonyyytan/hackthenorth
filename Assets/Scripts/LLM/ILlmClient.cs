using System;

namespace HackTheNorth.LLM
{
    /// <summary>
    /// The "search/research" call: given transcribed speech or a detected object label,
    /// returns structured text to show in a caption box.
    /// </summary>
    public interface ILlmClient
    {
        void Query(string prompt, Action<string> onResult);
    }
}
