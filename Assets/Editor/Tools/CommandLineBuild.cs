using UnityEditor;
using UnityEngine;

namespace HackTheNorth.EditorTools
{
    /// <summary>
    /// Headless Android build entry point for `Unity.exe -batchmode -quit -executeMethod`.
    /// Exists so a build can be triggered from the command line without a live Editor/MCP
    /// bridge session — useful when the bridge is unreliable (as it has been tonight) but a
    /// real on-device APK is still needed for testing.
    /// </summary>
    public static class CommandLineBuild
    {
        public static void BuildAndroidApk()
        {
            var options = new BuildPlayerOptions
            {
                scenes = new[] { "Assets/Scenes/SampleScene.unity" },
                locationPathName = "Builds/HackTheNorth.apk",
                target = BuildTarget.Android,
                options = BuildOptions.None,
            };

            var report = BuildPipeline.BuildPlayer(options);
            var summary = report.summary;

            Debug.Log($"CommandLineBuild: result={summary.result} " +
                $"totalErrors={summary.totalErrors} totalWarnings={summary.totalWarnings} " +
                $"outputPath={summary.outputPath} sizeBytes={summary.totalSize}");

            if (summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
            {
                EditorApplication.Exit(1);
            }
        }
    }
}
