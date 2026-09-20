using System;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Listens for server.py's UDP presence broadcast (see server.py's _broadcast_presence) and
    /// points FaceIdClient at whatever address it hears back from — so nobody has to hand-type
    /// or update a LAN IP before a demo, which is exactly the kind of thing that breaks on venue
    /// Wi-Fi when the router hands out a different address than expected. Falls back silently to
    /// FaceIdClient's own configured serverUrl if nothing is ever heard (e.g. discovery blocked
    /// by client isolation) — this only helps, never hard-requires a working broadcast.
    /// </summary>
    public class ServerDiscovery : MonoBehaviour
    {
        private const int DiscoveryPort = 41234;
        private const string Magic = "HACKTHENORTH_ID_SERVER:";

        [SerializeField] private FaceIdClient faceIdClient;

        private UdpClient client;
        private bool locked;

        private void Start()
        {
            try
            {
                client = new UdpClient(DiscoveryPort) { EnableBroadcast = true };
                client.BeginReceive(OnReceive, null);
            }
            catch (Exception e)
            {
                // Port in use / no network yet -- FaceIdClient just keeps its manually-set
                // serverUrl, so this is a soft failure, not a broken build.
                Debug.LogWarning($"ServerDiscovery: couldn't listen on UDP {DiscoveryPort}: {e.Message}");
            }
        }

        private void OnReceive(IAsyncResult result)
        {
            if (client == null) return; // stopped between the callback firing and running

            System.Net.IPEndPoint sender = null;
            byte[] data;
            try
            {
                data = client.EndReceive(result, ref sender);
            }
            catch (ObjectDisposedException)
            {
                return; // OnDestroy closed the socket while a receive was pending
            }

            string text = Encoding.ASCII.GetString(data);
            if (text.StartsWith(Magic) && sender != null)
            {
                string port = text.Substring(Magic.Length);
                string url = $"http://{sender.Address}:{port}";
                if (faceIdClient != null && faceIdClient.ServerUrl != url)
                {
                    faceIdClient.SetServerUrl(url);
                    Debug.Log($"ServerDiscovery: found identity server at {url}");
                }
                // Keep listening rather than latching once: if the server restarts on a
                // different machine/IP mid-demo, this quietly follows it.
            }

            client?.BeginReceive(OnReceive, null);
        }

        private void OnDestroy()
        {
            client?.Close();
            client = null;
        }
    }
}
