/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package tools.descartes.teastore.registryclient.test;

import java.io.IOException;
import java.io.PrintWriter;

import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/**
 * Test servlet that waits 6 seconds before responding with a hard coded product.
 * @author Joakim von Kistowski
 */
@WebServlet("/test4")
public class SlowTimeoutingServlet extends HttpServlet {
	
	private static final long serialVersionUID = 1L;
       
    /**
     * @see HttpServlet#HttpServlet()
     */
    public SlowTimeoutingServlet() {
        super();
    }

	/**
	 * {@inheritDoc}
	 * Waits 6 seconds before responding with a hard coded product
	 */
	protected void doGet(HttpServletRequest request, HttpServletResponse response)
			throws ServletException, IOException {
		try {
			Thread.sleep(6000);
		} catch (InterruptedException e) {
			System.out.println("Interrupted sleeping in the slow responding servlet.");
		}
		
		response.setContentType("application/json");    
		PrintWriter out = response.getWriter();
		out.print("{\"id\":18,\"name\":\"Dog Products\",\"description\":\"Products for Dogs.\"}");
		out.flush();
	}

	/**
	 * {@inheritDoc}
	 */
	protected void doPost(HttpServletRequest request, HttpServletResponse response)
			throws ServletException, IOException {
		doGet(request, response);
	}

}
